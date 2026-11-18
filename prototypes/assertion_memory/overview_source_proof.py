"""On-demand immutable source audit for already accepted, frozen canonical anchors.

No search, external fetch, new fact acceptance or artifact-completeness inference.
Bodies are loaded only for requested accepted references, under the query's raw
row watermark and recorded cutoff. Repeated anchors reuse exact proof bytes.
"""
from __future__ import annotations

import hashlib
import json

from .dialogue import _source_ref, event_role
from .model import ModelError
from .queue import _event
from .source_message_clock import projection as clock_projection

SOURCE_PROOF_PROTOCOL='overview-source-proof-v2:unified-attributed-origins-v1'
MAX_SOURCE_CHARACTERS=6000

class StaleSourceRevision(ModelError):
    """A frozen attempt cannot authorize recovery against a newer source run."""
    def __init__(self,expected,current):
        self.expected_revision=expected['id'];self.current_revision=current['id']
        super().__init__('Source revision advanced beyond the frozen overview; recapture required.')

RULES=(
 'An ordinary read of source_ids supplies canonical interpreted text. When exact original wording or '
 'source completeness is material, request the SAME read with include_source_evidence=true; use visible '
 'canonical IDs, never guess event IDs. source_evidence is frozen original proof tied to accepted canonical '
 'anchors, not another canonical fact. Its exact source_span, source_length and source_text_complete '
 'describe ONLY delivered source bytes. A complete utterance/source body does not establish a complete artifact '
 'or world coverage. Independently bind any artifact boundary/completeness declaration to its actual text '
 'before drawing an explicitly scoped textual inference; partial proof cannot establish exhaustive absence. '
 'Raw proof may verify canonical meaning or support independently reviewed scoped textual inference; '
 'unaccepted positive facts in raw proof MUST NOT silently become answer facts. New omitted positive meaning '
 'requires SourceReprocess and accepted canonical interpretation, or an explicit interpretation limitation. '
 'A newly recovered proposer-stated reason is positive attributed speech content too; calling it a '
 'textual inference does not bypass this boundary. Do not turn it into user endorsement, motive or '
 'verified world truth. Reading originals does not automatically reprocess sources. Source-audit '
 'reasons/IDs/hashes are audit metadata, never domain facts. Missing legacy originals remain unavailable; '
 'faithful attributed canonical reports may still answer positive questions without an original. Proof '
 'delivery/receipts alone never accept the whole answer: independent whole-answer review still applies. ')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class SourceProofResolver:
    """One build's selective raw read boundary; no raw I/O in construction."""
    def __init__(self,store,snapshot):
        self.store=store
        self.snapshot=snapshot
        self.events={}

    def _event(self,event_id):
        if event_id not in self.events:
            if self.snapshot.raw_event_watermark is None:
                return None
            with self.store.transaction() as connection:
                row=connection.execute('SELECT * FROM memory_raw_events WHERE id=? AND rowid<=? AND recorded_at<=?',
                    (event_id,self.snapshot.raw_event_watermark,self.snapshot.effective_recorded_as_of)).fetchone()
            self.events[event_id]=_event(row) if row else None
        return self.events[event_id]

    def omission_authority(self,row,proof,*,gap=None):
        from types import SimpleNamespace
        from .source_reprocessing import freeze, PROTOCOL
        from .source_omission import revision
        canonical=self.snapshot.assertions.get(row['id']) if gap is None else None
        origin=canonical['metadata'].get('event_id') if canonical else gap.get('event_id') if gap else None
        if gap is None:
            if not origin or self(row)['unavailable']:
                raise ModelError('Source omission has no accepted frozen canonical owner.')
        elif (not origin or gap not in self.snapshot.source_processing_evidence or not gap.get('gap') or
                gap.get('context_only') or self._event(origin) is None):
            raise ModelError('Source omission has no frozen attributable gap owner.')
        with self.store.transaction() as db:
            frozen=freeze(SimpleNamespace(store=self.store),origin,db)
            expected=revision(db,origin,watermark=self.snapshot.source_revision_watermark,
                cutoff=self.snapshot.effective_recorded_as_of)
            current=revision(db,origin)
            if (expected is not None and current is not None
                    and type(expected.get('id')) is int and type(current.get('id')) is int
                    and current['id']>expected['id']):
                # This never grants source/answer authority. Background work must
                # recapture; a fresh attempt still runs all owner and raw checks.
                raise StaleSourceRevision(expected,current)
            if (expected is None or revision(db,origin)!=expected or
                    (gap is None and row['id'] not in frozen.prior_ids) or
                    (gap is not None and (gap['run_id']!=expected['id'] or
                        gap.get('raw_source')!={'text':frozen.event.prompt,'actor':frozen.event.actor,
                            'source':frozen.event.source,'role':event_role(frozen.event)} or
                        gap['scope']!=frozen.event.scope))):
                raise ModelError('Source omission owner or source revision is unavailable or stale.')
            result=json.loads(db.execute('SELECT result FROM memory_extraction_runs WHERE id=?',(expected['id'],)).fetchone()[0])
            accepted=result.get('source_revision') or {}
            if (accepted.get('input_fingerprint')!=frozen.fingerprint or accepted.get('protocol')!=PROTOCOL):
                raise ModelError('Source omission requires the current source input and protocol.')
            if db.execute('SELECT 1 FROM memory_raw_events WHERE id IN (SELECT value FROM json_each(?)) '
                    'AND (rowid>? OR recorded_at>?) LIMIT 1',(json.dumps([event.id for event in frozen.events]),
                    self.snapshot.raw_event_watermark,self.snapshot.effective_recorded_as_of)).fetchone():
                raise ModelError('Source omission prefix is outside frozen raw authority.')
        return {'event_id':origin,'scope':frozen.event.scope,'input_fingerprint':frozen.fingerprint,
            'prior_ids':sorted(frozen.prior_ids),'source_revision':expected,
            'snapshot_sha256':digest(self.snapshot.descriptor())}

    def reuse(self,row,delivered):
        """Prove reuse from bytes already delivered, without acquiring any source."""
        refs=row.get('metadata',{}).get('evidence_refs',[])
        if not isinstance(refs,list) or not refs or any(not isinstance(ref,dict) or
                ref.get('event_id') not in self.events or self.events[ref['event_id']] is None for ref in refs):
            return None
        proof=self(row)
        available={body['proof_id'] for body in delivered['bodies']}
        if proof['unavailable'] or not proof['anchors'] or not {body['proof_id'] for body in proof['bodies']}<=available:
            return None
        return proof

    def __call__(self,row):
        key=row['id']
        unavailable=lambda reason: {'anchors':[],'bodies':[],
            'unavailable':[{'assertion_id':key,'reason':'source evidence unavailable: '+reason}]}
        canonical=self.snapshot.assertions.get(key)
        fields=('text','source','actor','scope','metadata','recorded_at','valid_at')
        if canonical is None or any(row.get(field)!=canonical.get(field) for field in fields):
            return unavailable('canonical proof is outside the frozen authorized record')
        metadata=canonical.get('metadata',{})
        refs=metadata.get('evidence_refs')
        if not isinstance(refs,list) or not refs or len(refs)>64:
            return unavailable('legacy canonical record has no bounded original references')
        origin=metadata.get('event_id')
        origin_refs=[ref for ref in refs if isinstance(ref,dict) and ref.get('event_id')==origin]
        if not isinstance(origin,str) or not origin_refs:
            return unavailable('accepted canonical origin event is absent from source references')
        origin_span=metadata.get('source_span')
        if origin_span is not None and (not isinstance(origin_span,dict) or not any(
                origin_span=={'start':ref.get('start'),'end':ref.get('end')} for ref in origin_refs)):
            return unavailable('accepted canonical origin span is absent from source references')
        anchors=[]; bodies={}
        try:
            for reference in refs:
                if not isinstance(reference,dict) or not isinstance(reference.get('event_id'),str):
                    raise ValueError('invalid source event identity')
                event=self._event(reference['event_id'])
                if event is None:
                    raise ValueError('source event is absent or outside frozen raw/time boundary')
                if event.scope!=canonical['scope']:
                    raise ValueError('source event is outside canonical scope')
                start,end=reference.get('start'),reference.get('end')
                if type(start) is not int or type(end) is not int or not 0<=start<end<=len(event.prompt):
                    raise ValueError('invalid exact source range')
                quote=event.prompt[start:end]
                # Reuse ingestion's exact quote/range/role construction. A stored
                # reference has no quote after compaction; a supplied one must match.
                checked=_source_ref(event,quote,reference.get('relation','statement'),start=start)
                if reference.get('role')!=checked['role'] or reference.get('quote',quote)!=quote:
                    raise ValueError('source role or quote does not match original')
                body_hash=text_hash(event.prompt)
                if reference.get('source_sha256',body_hash)!=body_hash:
                    raise ValueError('immutable source body hash mismatch')
                if origin==event.id:
                    if event.source!=canonical['source'] or event.actor!=canonical['actor']:
                        raise ValueError('origin source or actor does not match canonical attribution')
                    span=metadata.get('source_span')
                    if span=={'start':start,'end':end} and metadata.get('excerpt',quote)!=quote:
                        raise ValueError('origin quote does not match accepted canonical proof')
                # Short source bodies enable literal artifact-boundary review.
                # Large originals expose exact cited spans only; no clipped text
                # may masquerade as the whole source/artifact.
                left,right=(0,len(event.prompt)) if len(event.prompt)<=MAX_SOURCE_CHARACTERS else (start,end)
                if right-left>MAX_SOURCE_CHARACTERS:
                    raise ValueError('exact referenced span exceeds bounded source page; original not delivered')
                text=event.prompt[left:right]
                proof={'body_sha256':body_hash,'text_sha256':text_hash(text),'text':text,
                    'source_span':{'start':left,'end':right},'source_length':len(event.prompt),
                    'source_text_complete':left==0 and right==len(event.prompt)}
                proof_id=digest({k:v for k,v in proof.items() if k!='text'})
                bodies[proof_id]={'proof_id':proof_id,**proof}
                clocks=clock_projection(event.metadata,source=event.source)
                source={'event_id':event.id,'scope':event.scope,'actor':event.actor,'source':event.source,
                    'recorded_at':event.recorded_at,'base_available_at':event.recorded_at,
                    'timezone':event.metadata.get('timezone'),**clocks}
                anchors.append({'assertion_id':key,'reference':{k:v for k,v in checked.items() if k!='quote'},
                    'source':source,'proof_id':proof_id})
        except (ValueError,TypeError,ModelError) as error:
            return unavailable(str(error))
        return {'anchors':anchors,'bodies':[bodies[k] for k in sorted(bodies)],'unavailable':[]}


def bundle(proofs):
    bodies={}; anchors={}; unavailable={}
    for proof in proofs:
        bodies.update({row['proof_id']:row for row in proof['bodies']})
        anchors.update({digest(row):row for row in proof['anchors']})
        unavailable.update({digest(row):row for row in proof['unavailable']})
    value={'role':'source_evidence','protocol':SOURCE_PROOF_PROTOCOL,
        'use':'verification_and_scoped_textual_inference_only_not_new_positive_facts',
        'artifact_coverage':'not_inferred_from_source_body_completeness',
        'bodies':[bodies[k] for k in sorted(bodies)],'anchors':[anchors[k] for k in sorted(anchors)],
        'unavailable':[unavailable[k] for k in sorted(unavailable)]}
    return {**value,'delivery_sha256':digest(value)}
