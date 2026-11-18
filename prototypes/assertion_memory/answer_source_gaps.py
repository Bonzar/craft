"""Pinned source limitations for answer review, never positive domain evidence.

Raw bodies are available only to the independent reviewer. Stable means a parsed
semantic coverage verdict exists, not merely that a queue job stopped retrying.
"""
from __future__ import annotations
import hashlib
import json

MAX_GAPS = 16
MAX_BODY_CHARACTERS = 16_000


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def semantic_terminal(result):
    revision=result.get('source_revision',{})
    receipts=[row for row in result.get('context_recovery',[]) if row.get('operation')=='source_coverage']
    if (any(row.get('operational_error') for row in result.get('context_recovery',[]))
            or revision.get('status')!='unresolved' or not receipts
            or any(row.get('kind')=='source_audit' for row in result.get('reviews',[]))):
        return False
    parsed=lambda row: (bool(row.get('protocol')) and type(row.get('attempt')) is int
                        and isinstance(row.get('omissions'),list))
    if all(parsed(row) for row in receipts):
        return True
    # An exhausted repair is terminal only with a program-derived proof that
    # the complete target had no authorized antecedent to recover. This adds no
    # meaning and leaves the unresolved source inventory/history unchanged.
    from .dialogue_context import MAX_RECOVERY_ROUNDS
    recovery=receipts[-1].get('recovery')
    return (len(receipts)>1 and all(parsed(row) for row in receipts[:-1])
            and recovery=={'schema':'source-context-exhaustion/v1','kind':'empty_authorized_prefix',
                'event_id':revision.get('event_id'),'target_complete':True,
                'authorized_preceding_count':0,'unavailable_preceding_count':0,
                'recovery_rounds':MAX_RECOVERY_ROUNDS}
            and result.get('context_truncated') is False
            and result.get('context_event_ids')==[revision.get('event_id')]
            and not revision.get('assertion_ids')
            and all(row.get('kind')=='source_coverage' for row in result.get('reviews',[])))


def freeze(connection, cutoff, run_watermark, raw_watermark, *, include_raw=True):
    if raw_watermark is None: return ()  # Legacy snapshots cannot acquire new raw sources.
    columns='e.id,e.scope,e.recorded_at,e.actor,e.source,e.metadata'+(',e.prompt' if include_raw else '')
    rows=connection.execute('SELECT '+columns+',r.id AS run_id,r.completed_at,r.result FROM memory_raw_events e '
        'LEFT JOIN memory_extraction_runs r ON r.id=(SELECT r2.id FROM memory_extraction_runs r2 '
        'WHERE r2.event_id=e.id AND r2.id<=? AND r2.completed_at<=? ORDER BY r2.completed_at DESC,r2.id DESC LIMIT 1) '
        'WHERE e.rowid<=? AND e.recorded_at<=? ORDER BY e.scope,e.id',
        (run_watermark or 0,cutoff,raw_watermark,cutoff)).fetchall()
    output=[]
    for row in rows:
        result=json.loads(row['result']) if row['result'] else {}
        revision=result.get('source_revision',{})
        from .queue import _current_reviews
        reviews=[r for r in _current_reviews(result.get('reviews',[])) if r.get('status') in ('pending','unresolved')
                 and r.get('kind')!='branch_relevance']
        from .dialogue import source_role
        role=source_role(row['actor'],json.loads(row['metadata']))
        context_only=False
        gap=revision.get('status') in ('unresolved','failed') or bool(reviews)
        entry={'scope':row['scope'],'event_id':row['id'],'run_id':row['run_id'],
               'completed_at':row['completed_at'],'recorded_at':row['recorded_at'],
               'protocol':revision.get('protocol'),'input_fingerprint':revision.get('input_fingerprint'),
               'status':revision.get('status','legacy' if row['run_id'] else 'pending'),
               'terminal_semantic':semantic_terminal(result),'gap':gap,'context_only':context_only}
        if gap and include_raw:
            entry['raw_source']={'text':row['prompt'],'actor':row['actor'],'source':row['source'],'role':role}
        output.append(entry)
    return tuple(output)


def inventory(evidence, scopes):
    eligible=[row for row in evidence if not scopes or row['scope'] in scopes]
    gaps=[row for row in eligible if row['gap']]
    selected=[]; characters=0
    for row in gaps:
        size=len(json.dumps(row,ensure_ascii=False))
        if len(selected)<MAX_GAPS and characters+size<=MAX_BODY_CHARACTERS:
            selected.append(row); characters+=size
    identities=lambda rows:[{key:value for key,value in row.items() if key!='raw_source'} for row in rows]
    return {'role':'source_gap_inventory','gaps':selected,'total':len(gaps),
            'complete':len(selected)==len(gaps),'unassessed_count':len(gaps)-len(selected),
            'frontier_sha256':digest(identities(eligible)),'inventory_sha256':digest(identities(gaps))}


def public_inventory(value):
    return {**value,'gaps':[{key:item for key,item in row.items() if key!='raw_source'} for row in value['gaps']]}
