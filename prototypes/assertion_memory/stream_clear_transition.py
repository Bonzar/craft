"""Auth-bound operational stream continuity after an owner database clear."""
from __future__ import annotations
from collections.abc import Iterable
import hmac
import re

PROTOCOL='owner-clear-v1'
CUTOVER='memory_database_clear_cutover'
TRANSITIONS='memory_database_clear_stream_transitions'
EXPECTED='memory_database_clear_expected_streams'
WITNESS='owner-clear-stream-authority-v1'
UUID=re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')

def _pair(value, field):
    if not isinstance(value,str) or UUID.fullmatch(value) is None:
        raise ValueError('Invalid '+field)
    return value

def schema(db):
    db.execute(f'''CREATE TABLE IF NOT EXISTS {CUTOVER}(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation TEXT NOT NULL, cleared_at TEXT NOT NULL)''')
    db.execute(f'''CREATE TABLE IF NOT EXISTS {TRANSITIONS}(
        prior_digest BLOB PRIMARY KEY, successor_digest BLOB NOT NULL UNIQUE, ready INTEGER NOT NULL DEFAULT 0 CHECK(ready IN (0,1))) WITHOUT ROWID''')

def witnesses(db,tables) -> Iterable[tuple[str,str,str,str,str,str]]:
    # Verify a previously enrolled producer, not a caller label. This adapter
    # to the deployed authenticated authority may be replaced by a generalized
    # stream-to-principal binding without changing this transport protocol.
    if not {'memory_ingest_streams','memory_fresh_mac_streams'}<=tables:
        return ()
    return db.execute('''SELECT s.producer_id,s.session_id,s.stream_id,s.epoch,s.lifecycle,s.lease_until
        FROM memory_ingest_streams AS s JOIN memory_fresh_mac_streams AS a
        ON s.stream_id=a.stream_id AND s.session_id=a.session_id''')

def unsupported_active_stream_exists(db,tables,now):
    """Do not erase an active epoch lacking an authenticated rearm witness."""
    if 'memory_ingest_streams' not in tables:
        return False
    predicate=("SELECT 1 FROM memory_ingest_streams AS s "
               "WHERE s.lifecycle='active' AND s.lease_until>?")
    if 'memory_fresh_mac_streams' in tables:
        predicate += (" AND NOT EXISTS (SELECT 1 FROM memory_fresh_mac_streams AS a "
                      "WHERE a.stream_id=s.stream_id AND a.session_id=s.session_id)")
    return db.execute(predicate+' LIMIT 1',(now,)).fetchone() is not None


def finish_clear(db,generation,cleared_at):
    schema(db)
    db.execute(f'DELETE FROM {TRANSITIONS}')
    db.execute(f'CREATE TABLE IF NOT EXISTS {EXPECTED}(prior_digest BLOB PRIMARY KEY) WITHOUT ROWID')
    db.execute(f'DELETE FROM {EXPECTED}')
    db.execute(f"""INSERT INTO {CUTOVER} VALUES(1,?,?)
        ON CONFLICT(singleton) DO UPDATE SET generation=excluded.generation,cleared_at=excluded.cleared_at""",
        (generation,cleared_at))

def re_register(db,producer_id,session_id,previous_stream_id,previous_epoch):
    from .database_clear import STATE,_fingerprint,revision,was_cleared
    from .ingest_streams import _identifier,_load,_public,register,schema as ingest_schema
    from .fresh_processing import is_authenticated_fresh_mac,before_register,admit_stream
    _identifier(producer_id,'producer_id')
    _identifier(session_id,'session_id')
    previous_stream_id=_pair(previous_stream_id,'previous stream identity')
    previous_epoch=_pair(previous_epoch,'previous epoch identity')
    ingest_schema(db)
    current=db.execute('SELECT stream_id FROM memory_ingest_streams WHERE producer_id=? AND session_id=?',
                       (producer_id,session_id)).fetchone()
    state=_load(db,current[0]) if current else None
    if state and (state['stream_id'],state['epoch'])==(previous_stream_id,previous_epoch):
        # Ordinary existing registration never grants a new epoch or re-admits
        # an event, even when the upgraded client supplied its previous pair.
        return _public(state)
    old=(producer_id,session_id,previous_stream_id,previous_epoch)
    if not is_authenticated_fresh_mac() or not was_cleared(db,WITNESS,*old):
        raise ValueError('No authenticated cleared stream witness')
    schema(db)
    cutover=db.execute(f'SELECT generation,cleared_at FROM {CUTOVER} WHERE singleton=1').fetchone()
    if not cutover or cutover[0]!=revision(db):
        raise ValueError('No current cleared stream generation')
    # Old and successor identity receipts are opaque, one authorized replacement
    # per witness and generation. A later ordinary epoch CAS supersedes replay.
    salt=db.execute(f'SELECT salt FROM {STATE} WHERE singleton=1').fetchone()[0]
    prior=_fingerprint(salt,'owner-clear-prior-v1',(cutover[0],*old))
    claim=db.execute(f'SELECT successor_digest FROM {TRANSITIONS} WHERE prior_digest=?',(prior,)).fetchone()
    if state is None:
        if claim or not before_register(db,producer_id,session_id,None):
            raise ValueError('Cannot claim cleared stream')
        state=register(db,producer_id,session_id)
        admit_stream(db,state)
    enrolled=db.execute('SELECT 1 FROM memory_fresh_mac_streams WHERE session_id=? AND stream_id=?',
                        (session_id,state['stream_id'])).fetchone()
    if not enrolled:
        raise ValueError('Current stream is not bound to this enrolled authority')
    successor=_fingerprint(salt,'owner-clear-successor-v1',
                           (cutover[0],producer_id,session_id,state['stream_id'],state['epoch']))
    if claim and not hmac.compare_digest(claim[0],successor):
        raise ValueError('Cleared stream witness was superseded')
    if not claim:
        if any(state[k]!=0 for k in ('report_seq','observed_seq','acked_through')):
            raise ValueError('Current stream already has a different frontier')
        assigned=db.execute(f'SELECT 1 FROM {TRANSITIONS} WHERE successor_digest=?',(successor,)).fetchone()
        if assigned:
            raise ValueError('Current stream was already assigned a reset witness')
        db.execute(f'INSERT INTO {TRANSITIONS}(prior_digest,successor_digest) VALUES(?,?)',(prior,successor))
    return _public(state)|{'database_clear_reset':{
        'protocol':PROTOCOL,'generation':cutover[0],'cleared_at':cutover[1],
        'previous_stream_id':previous_stream_id,'previous_epoch':previous_epoch,
        'producer_id':producer_id,'session_id':session_id}}


def acknowledge(db,state,generation):
    """A single explicit statement that the authenticated producer re-armed."""
    from .database_clear import STATE,_fingerprint,revision
    from .fresh_processing import is_authenticated_fresh_mac
    if (not is_authenticated_fresh_mac() or not isinstance(generation,str)
            or not re.fullmatch('[a-f0-9]{32}',generation) or generation!=revision(db)):
        raise ValueError('No authenticated current stream reset to acknowledge')
    schema(db)
    row=db.execute(f'SELECT salt FROM {STATE} WHERE singleton=1').fetchone()
    if row is None:
        raise ValueError('No cleared stream generation')
    successor=_fingerprint(row[0],'owner-clear-successor-v1',
        (generation,state['producer_id'],state['session_id'],state['stream_id'],state['epoch']))
    if db.execute(f'UPDATE {TRANSITIONS} SET ready=1 WHERE successor_digest=?',(successor,)).rowcount!=1:
        raise ValueError('Reset acknowledgement does not match the successor stream')
    return state|{'database_clear_ready_generation':generation}


def require_ready(db,state):
    """A reset successor cannot persist any delivery before its exact ready ack."""
    from .database_clear import STATE,_fingerprint,revision
    if not db.execute("SELECT 1 FROM sqlite_schema WHERE name=? AND type='table'",(TRANSITIONS,)).fetchone():
        return
    row=db.execute(f'SELECT salt FROM {STATE} WHERE singleton=1').fetchone()
    if row is None:
        raise ValueError('Missing database reset state')
    proof=_fingerprint(row[0],'owner-clear-successor-v1',
        (revision(db),state['producer_id'],state['session_id'],state['stream_id'],state['epoch']))
    result=db.execute(f'SELECT ready FROM {TRANSITIONS} WHERE successor_digest=?',(proof,)).fetchone()
    if result is not None and result[0]!=1:
        raise ValueError('Reset successor capture is not re-armed')


def capture_rearm_status(db,generation):
    """Opaque owner dashboard status; this is not proof of all sessions."""
    if generation=='0'*32:
        return 'not_cleared'
    if not db.execute("SELECT 1 FROM sqlite_schema WHERE name=? AND type='table'",(EXPECTED,)).fetchone():
        return 'unknown'
    row=db.execute(f'SELECT generation FROM {CUTOVER} WHERE singleton=1').fetchone()
    if row is None or row[0]!=generation:
        return 'unknown'
    expected,ready=db.execute(f"""SELECT count(*),count(t.ready) FROM {EXPECTED} e
        LEFT JOIN {TRANSITIONS} t ON e.prior_digest=t.prior_digest AND t.ready=1""").fetchone()
    if not expected:
        return 'not_required'
    return 'confirmed' if ready==expected else 'waiting'
