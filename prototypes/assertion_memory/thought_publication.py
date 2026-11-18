"""Short atomic publication with preserved alternatives and one bounded rebuild."""
import hashlib
import json
from .thought_store import ThoughtConflict
from .thought_engine_contract import PROTOCOL

UNRESOLVED = ('Independently verified parallel alternative; compatibility with another answer '
              'to the same question remains unresolved. Completion order selects no primary answer.')


def _one(engine,draft,bases,origin,target,whole_refresh=None):
    current = engine.thoughts.get(target.id) if target else None
    alternative = target is not None and current is not None and current.version!=target.version
    metadata = {'protocol':PROTOCOL,'verification':'accepted'}
    if whole_refresh is not None and target is not None and target.metadata.get('publication'):
        metadata['publication'] = dict(target.metadata['publication'])
    if alternative:
        metadata['publication'] = {'state':'parallel_alternative','target_id':target.id,
            'prepared_target_version':target.version,'observed_target_version':current.version,
            'reconciliation':'unresolved'}
    thought = engine.thoughts.publish(**draft,origin=origin or {'kind':'inference'},bases=bases,
        metadata=metadata,thought_id=None if alternative or target is None else target.id,
        expected_version=None if alternative or target is None else target.version)
    if alternative:
        engine.thoughts.connect(thought.id,current.id,UNRESOLVED,
            source_version=thought.version,target_version=current.version,
            metadata={'reconciliation':'unresolved','prepared_target_version':target.version})
    if whole_refresh is not None and (thought.id, thought.version) != (target.id, target.version):
        questions = whole_refresh[0]['questions']
        frontier = 'whole-refresh:' + hashlib.sha256(json.dumps(
            [thought.text, bases, questions], sort_keys=True).encode()).hexdigest()
        for question in questions:
            engine.thoughts.save_question_check(thought, question, frontier)
    return thought



def _fresh_material(engine,verified,origin=None,*,whole_refresh=None):
    question = verified[0][0].get('question') if len(verified)==1 else None
    current,raw_ids = {},set()
    for _,bases in verified:
        for basis in bases:
            if 'thought_id' in basis:
                thought = engine.thoughts.get(basis['thought_id'])
                if thought is None:
                    raise ThoughtConflict('A required publication basis is no longer active')
                current[thought.id] = thought
            elif 'event_id' in basis:
                raw_ids.add(basis['event_id'])
    purpose = '\n'.join(whole_refresh[0]['questions']) if whole_refresh is not None else question
    records,bases = engine._current_material(purpose or 'Preserve the verified source meaning.',
        list(current.values()),known_ids=list(current)) if current else ([],[])
    for event_id in sorted(raw_ids):
        packet = engine._raw_packet(event_id)
        if not packet:
            raise ThoughtConflict('A required raw publication basis is no longer admitted')
        records,bases = engine._merge_material(records,bases,[row for row,_ in packet],[basis for _,basis in packet])
    if not records:
        raise ThoughtConflict('No admitted current grounds remain for publication')
    primary = (origin or {}).get('event_id')
    if primary is not None:
        pairs = [(dict(record,role='source' if basis.get('event_id')==primary else 'context'),basis)
                 for record,basis in zip(records,bases)]
        if not any(record['role']=='source' for record,_ in pairs):
            raise ThoughtConflict('The primary attributed source is no longer admitted')
        pairs.sort(key=lambda pair:pair[0]['role']!='source')
        records,bases = [record for record,_ in pairs],[basis for _,basis in pairs]
    if whole_refresh is not None:
        records,bases = [whole_refresh[0], *records],[whole_refresh[1], *bases]
    return question,records,bases


def _recoverable(error):
    error.recoverable = True
    error.requires_reread = True
    return error


def publish(engine,verified,*,origin=None,target=None,emit=True,whole_refresh=None):
    prepared = verified
    outer_writer = engine.store._writing
    for attempt in range(2):
        try:
            with engine.store.transaction(write=True):
                published = [_one(engine,draft,bases,origin,target,whole_refresh) for draft,bases in prepared]
            break
        except ThoughtConflict as conflict:
            if attempt or outer_writer:
                # An owning source transaction must retry after releasing its writer.
                raise _recoverable(conflict)
            try:
                question,records,bases = _fresh_material(engine,prepared,origin,whole_refresh=whole_refresh)
            except ThoughtConflict as unavailable:
                raise _recoverable(unavailable) from conflict
            engine._emit('publication_rebuild',reason='Supporting versions changed before publication; reread current material once',
                selected=bases,attempt=1)
            prepared = engine._prepare(records,bases,question=question,origin=origin)
    if emit:
        for thought in published:
            engine._emit('publication',thought=engine._basis(thought),bases=thought.bases,
                publication_state=thought.metadata.get('publication',{}).get('state','published'))
    return published
