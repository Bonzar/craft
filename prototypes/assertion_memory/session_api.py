"""Bounded, model-free reads of stored sessions and real source anchors.

Accepts an existing SQLite connection, including mode=ro/query_only connections.
Never constructs the engine/queue, creates a schema, or opens a write transaction.
Session membership is raw-event provenance, not topical relevance or truth.
"""
from __future__ import annotations
import json


def _id(value, name):
    if not isinstance(value,str) or not value.strip() or len(value)>2048:
        raise ValueError(name+' must be a nonempty ID of at most 2048 characters')
    return value


# Only a literal native metadata ID identifies a source chat. URI text, scope,
# assertion metadata and logical capture grouping never supply native identity.
_NATIVE_SESSION = ("CASE WHEN json_type(e.metadata,'$.native_session_id')='text' "
    "AND length(trim(json_extract(e.metadata,'$.native_session_id')))>0 "
    "THEN json_extract(e.metadata,'$.native_session_id') END")
_SOURCE_SESSION = 'coalesce('+_NATIVE_SESSION+',e.session_id)'
_ORIGIN_COLUMNS = ('e.id,e.session_id AS capture_session_id,'+_NATIVE_SESSION+
    ' AS native_session_id,'+_SOURCE_SESSION+' AS source_session_id')


def _identity_basis(native_count, total):
    return ('metadata.native_session_id' if native_count==total else
            'stored_session_id_fallback' if not native_count else
            'mixed_native_and_stored_fallback')


def _provenance_record(record_id, origins, *, complete, unresolved):
    sessions=sorted({row['source_session_id'] for row in origins})
    return {'record_id':record_id,'session_ids':sessions,'source_session_ids':sessions,
        'capture_session_ids':sorted({row['capture_session_id'] for row in origins}),
        'native_session_ids':sorted({row['native_session_id'] for row in origins if row['native_session_id'] is not None}),
        'session_id_basis':_identity_basis(sum(row['native_session_id'] is not None for row in origins),len(origins)) if origins else 'unknown',
        'membership':'mixed' if len(sessions)>1 else 'single' if sessions and complete else 'unknown',
        'complete':complete,'origin_count':len({row['id'] for row in origins}),
        'unresolved_reference_count':unresolved}


class SessionAPI:
    def __init__(self, connection):
        self.connection=connection

    def _page(self, offset, limit, snapshot_id):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=200:
            raise ValueError('offset must be nonnegative and limit between 1 and 200')
        if snapshot_id is not None and (type(snapshot_id) is not int or snapshot_id<0):
            raise ValueError('snapshot_id must be nonnegative')
        return snapshot_id if snapshot_id is not None else self.connection.execute(
            'SELECT coalesce(max(rowid),0) FROM memory_raw_events').fetchone()[0]

    def sessions(self, *, offset=0, limit=50, snapshot_id=None):
        snapshot=self._page(offset,limit,snapshot_id)
        total=self.connection.execute('SELECT count(DISTINCT '+_SOURCE_SESSION+') '
            'FROM memory_raw_events e WHERE e.rowid<=?',(snapshot,)).fetchone()[0]
        rows=self.connection.execute('SELECT '+_SOURCE_SESSION+' AS session_id,'
            'count(*) AS message_count,min(e.recorded_at) AS first_recorded_at,'
            'max(e.recorded_at) AS last_recorded_at,count(DISTINCT e.session_id) AS capture_session_count,'
            'CASE WHEN count(DISTINCT e.session_id)=1 THEN min(e.session_id) END AS capture_session_id,'
            'sum(('+_NATIVE_SESSION+') IS NOT NULL) AS native_message_count '
            'FROM memory_raw_events e WHERE e.rowid<=? GROUP BY '+_SOURCE_SESSION+
            ' ORDER BY session_id LIMIT ? OFFSET ?',(snapshot,limit,offset))
        items=[]
        for row in rows:
            native_count=row['native_message_count'];total_count=row['message_count']
            items.append(dict(row,source_session_id=row['session_id'],
                native_session_id=row['session_id'] if native_count else None,
                fallback_message_count=total_count-native_count,
                session_id_basis=_identity_basis(native_count,total_count),title=None,title_source=None))
        return dict(self._envelope('sessions',items,total,offset,snapshot,'stored_raw_events'),
                    session_identity_basis='explicit_native_session_or_stored_fallback')

    def messages(self, session_id, *, turn_id=None, message_id=None, offset=0, limit=50, snapshot_id=None):
        _id(session_id,'session_id'); snapshot=self._page(offset,limit,snapshot_id)
        # Preserve exact legacy capture-group lookup in addition to source IDs.
        # A logical capture group may span several separately labeled source chats.
        where='e.rowid<=? AND ('+_SOURCE_SESSION+'=? OR e.session_id=?)'
        args=[snapshot,session_id,session_id]
        if turn_id is not None:
            where+=' AND e.turn_id=?'; args.append(_id(turn_id,'turn_id'))
        if message_id is not None:
            where+=" AND json_extract(e.metadata,'$.message_id')=?"; args.append(_id(message_id,'message_id'))
        total=self.connection.execute('SELECT count(*) FROM memory_raw_events e WHERE '+where,args).fetchone()[0]
        rows=self.connection.execute("SELECT e.id AS event_id,"+_SOURCE_SESSION+" AS session_id,"
            "e.session_id AS capture_session_id,"+_NATIVE_SESSION+" AS native_session_id,"
            "e.turn_id,json_extract(e.metadata,'$.native_turn_id') AS native_turn_id,"
            "json_extract(e.metadata,'$.message_id') AS message_id,json_extract(e.metadata,'$.role') AS role,"
            "e.actor,e.recorded_at,length(e.prompt) AS text_characters,p.position "
            "FROM memory_raw_events e LEFT JOIN memory_event_positions p ON p.event_id=e.id WHERE "+where+
            ' ORDER BY coalesce(p.position,e.rowid),e.rowid LIMIT ? OFFSET ?',[*args,limit,offset])
        items=[dict(row,source_session_id=row['session_id'],
            session_id_basis=_identity_basis(int(row['native_session_id'] is not None),1),
            turn_id_basis='stored_capture_turn_id') for row in rows]
        return dict(self._envelope('messages',items,total,offset,snapshot,'stored_raw_events'),session_id=session_id,
                    lookup_basis='exact_source_or_capture_session_id',
                    notice='session_id/source_session_id use explicit native identity or stored fallback; capture_session_id is the logical intake group. '
                           'A capture-group lookup may include several source chats. turn_id is the stored capture identity; '
                           'native_turn_id is explicit native provenance or null. Read bodies with memory_source(event_id).')

    @staticmethod
    def _envelope(key, items, total, offset, snapshot, basis):
        while items and len(json.dumps(items,ensure_ascii=False))>22000:
            items.pop()
        if not items and offset<total:
            raise ValueError('source identity exceeds response budget')
        nxt=offset+len(items)
        return {key:items,'total':total,'offset':offset,'next_offset':nxt if nxt<total else None,
                'snapshot_id':snapshot,'count_basis':basis,'response_complete':nxt>=total}

    def provenance(self, record_ids):
        if not isinstance(record_ids,list) or not 1<=len(record_ids)<=100:
            raise ValueError('record_ids must contain 1 to 100 IDs')
        for value in record_ids:_id(value,'record_id')
        records=[]
        for rid in record_ids:
            raw=self.connection.execute('SELECT '+_ORIGIN_COLUMNS+' FROM memory_raw_events e WHERE e.id=?',(rid,)).fetchone()
            if raw:
                records.append(_provenance_record(rid,[raw],complete=True,unresolved=0));continue
            row=self.connection.execute('SELECT metadata FROM memory_assertions WHERE assertion_id=?',(rid,)).fetchone()
            refs=json.loads(row['metadata']).get('evidence_refs',[]) if row else []
            origins=[];unresolved=0
            # Oversized fanout fails closed instead of guessing a single session.
            if not isinstance(refs,list) or len(refs)>1000:
                records.append(_provenance_record(rid,[],complete=False,unresolved=None));continue
            for ref in refs:
                if not isinstance(ref,dict):unresolved+=1;continue
                origin=self.connection.execute('SELECT '+_ORIGIN_COLUMNS+',length(e.prompt) AS n '
                    'FROM memory_raw_events e WHERE e.id=?',(ref.get('event_id'),)).fetchone()
                start,end=ref.get('start'),ref.get('end')
                if not origin or type(start) is not int or type(end) is not int or not 0<=start<end<=origin['n']:
                    unresolved+=1;continue
                origins.append(origin)
            records.append(_provenance_record(rid,origins,complete=bool(refs) and unresolved==0,unresolved=unresolved))
        if len(json.dumps(records,ensure_ascii=False))>24000:
            raise ValueError('provenance response exceeds budget; request fewer records or inspect source references')
        return {'records':records,'membership_basis':'validated_raw_event_evidence_refs',
                'session_identity_basis':'explicit_native_session_or_stored_fallback','response_complete':True}
