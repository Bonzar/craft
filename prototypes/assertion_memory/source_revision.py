"""Versioned interpretation membership in the immutable extraction-run ledger.

This module checks structural/provenance publication obligations; it does not
perform semantic verification. Publishers supply independent verifier receipts.
Legacy cumulative results remain a baseline, never complete replacements.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .storage import _timestamp

SCHEMA = 'source-interpretation/v1'
_CURRENT = object()


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('source revision requires nonempty identity/reason')
    return value


def _ids(value):
    if not isinstance(value, list) or any(not isinstance(key,str) or not key for key in value):
        raise ValueError('source revision assertion IDs must be a list of strings')
    if len(set(value)) != len(value):
        raise ValueError('duplicate source revision assertion ID')
    return set(value)


def watermark(connection):
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_extraction_runs'").fetchone()
    return connection.execute('SELECT coalesce(max(id),0) FROM memory_extraction_runs').fetchone()[0] if exists else 0


def _proof(connection, target, proof):
    if not isinstance(proof,dict) or proof.get('status') != 'accepted':
        raise ValueError('source reconciliation requires accepted independent verification')
    _text(proof.get('verifier')); _text(proof.get('reason'))
    refs = proof.get('evidence_refs')
    if not isinstance(refs,list) or not refs:
        raise ValueError('source reconciliation requires exact raw references')
    anchored = False
    for ref in refs:
        if not isinstance(ref,dict) or set(ref) != {'event_id','start','end'}:
            raise ValueError('source proof must contain compact references only')
        row = connection.execute('SELECT * FROM memory_raw_events WHERE id=?',(ref['event_id'],)).fetchone()
        start,end = ref['start'],ref['end']
        if (row is None or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(row['prompt'])
                or row['session_id'] != target['session_id'] or row['scope'] != target['scope']):
            raise ValueError('source proof is outside its authorized source')
        anchored = anchored or row['id'] == target['id']
    if not anchored:
        raise ValueError('source verification must anchor the target event')


def _reject_other_owner(connection, event_id, ids):
    # Publication validates against immutable ledger membership, never incidental
    # assertion.metadata.event_id. New output cannot steal another event's claim.
    if not ids or watermark(connection) == 0:
        return
    rows = connection.execute('SELECT result FROM memory_extraction_runs WHERE event_id != ?', (event_id,))
    for row in rows:
        other = json.loads(row['result'])
        if not isinstance(other,dict):
            raise ValueError('invalid immutable extraction result')
        manifest = other.get('source_revision',other)
        if not isinstance(manifest,dict):
            raise ValueError('invalid immutable source revision')
        if ids & _ids(manifest.get('assertion_ids',[])):
            raise ValueError('assertion is already owned by another raw event')


def validate_result(connection, event_id, result, *, prior_ids):
    return _validate_result(connection,event_id,result,prior_ids=prior_ids)


def _validate_result(connection, event_id, result, *, prior_ids, owners=None):
    """Validate a new-contract result; return its manifest without mutating data.

    Complete results explicitly reconcile every prior ID. Unresolved coverage
    can attest retained IDs and introduce independently verified partial meaning;
    only positive withdrawals remove prior IDs. Failed runs cannot add meaning.
    The caller must additionally fence the authorized input fingerprint and lease.
    """
    if not isinstance(result,dict) or not isinstance(result.get('source_revision'),dict):
        raise ValueError('missing versioned source revision')
    revision = result['source_revision']
    required = {'schema','event_id','protocol','input_fingerprint','status','assertion_ids','reconciliation'}
    if set(revision) != required or revision['schema'] != SCHEMA or revision['event_id'] != event_id:
        raise ValueError('invalid source revision contract/owner')
    _text(revision['protocol']); _text(revision['input_fingerprint'])
    status = revision['status']
    if status not in ('accepted_complete','unresolved','failed'):
        raise ValueError('invalid source revision outcome')
    members = _ids(revision['assertion_ids'])
    if owners is None:
        _reject_other_owner(connection,event_id,members)
    elif any(key in owners and owners[key] != event_id for key in members):
        raise ValueError('assertion is already owned by another raw event')
    target = connection.execute('SELECT * FROM memory_raw_events WHERE id=?',(event_id,)).fetchone()
    if target is None:
        raise ValueError('unknown source revision owner')
    reconciliation = revision['reconciliation']
    if not isinstance(reconciliation,list):
        raise ValueError('source reconciliation must be a list')
    decisions = {}
    for item in reconciliation:
        if not isinstance(item,dict) or set(item) != {'assertion_id','decision','verification'}:
            raise ValueError('invalid source reconciliation receipt')
        key = _text(item['assertion_id'])
        if key in decisions:
            raise ValueError('duplicate source reconciliation')
        decision = item['decision']
        if decision not in ('retained','introduced','withdrawn'):
            raise ValueError('invalid source reconciliation decision')
        if decision in ('retained','withdrawn') and key not in prior_ids:
            raise ValueError('source reconciliation references a noncurrent prior ID')
        if decision == 'introduced' and key in prior_ids:
            raise ValueError('current source membership must be explicitly retained')
        row = connection.execute('SELECT * FROM memory_assertions WHERE assertion_id=?',(key,)).fetchone()
        if row is None or row['scope'] != target['scope'] or row['episode'] != target['session_id']:
            raise ValueError('source interpretation is absent or belongs to another source scope')
        _proof(connection,target,item['verification'])
        decisions[key] = decision
    kept = {key for key,decision in decisions.items() if decision != 'withdrawn'}
    if status == 'accepted_complete':
        if kept != members or not set(prior_ids) <= decisions.keys():
            raise ValueError('complete source revision must reconcile exact membership and every prior ID')
    elif status == 'unresolved':
        if kept != members:
            raise ValueError('partial source membership must match its positive verification receipts')
    elif members or any(decision != 'withdrawn' for decision in decisions.values()):
        raise ValueError('failed source revision can only positively withdraw prior IDs')
    return revision


@dataclass(frozen=True)
class Selection:
    inactive_ids: frozenset
    evidence: tuple

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.evidence,sort_keys=True).encode()).hexdigest()


def selection(connection, recorded_as_of=None, run_watermark=None):
    """Latest complete membership plus verified partial additions minus withdrawals.

    Ownership comes only from ledger references. All known ledger rows up to the
    watermark establish ownership, even when publication is after an as-of query;
    that prevents a future interpretation from leaking in as a direct assertion.
    """
    maximum = watermark(connection) if run_watermark is None else run_watermark
    if maximum == 0:
        return Selection(frozenset(),())
    cutoff = _timestamp(recorded_as_of) if recorded_as_of is not None else None
    rows = connection.execute('SELECT * FROM memory_extraction_runs WHERE id<=? ORDER BY completed_at,id',(maximum,)).fetchall()
    owners, active, selected, evidence = {}, {}, {}, []
    modern, selected_modern = set(), set()
    for row in rows:
        event = row['event_id']
        result = json.loads(row['result'])
        if not isinstance(result,dict):
            raise ValueError('invalid immutable extraction result')
        prior = active.setdefault(event,set())
        eligible = cutoff is None or _timestamp(row['completed_at']) <= cutoff
        if 'source_revision' not in result:
            ids = _ids(result.get('assertion_ids',[]))
            # Historical legacy outputs are cumulative bookkeeping, not replacements.
            if event not in modern:
                prior.update(ids)
            if eligible and event not in selected_modern:
                selected.setdefault(event,set()).update(ids)
        else:
            revision = _validate_result(connection,event,result,prior_ids=prior,owners=owners)
            ids = set(revision['assertion_ids'])
            withdrawn = {item['assertion_id'] for item in revision['reconciliation'] if item['decision']=='withdrawn'}
            introduced = {item['assertion_id'] for item in revision['reconciliation'] if item['decision']=='introduced'}
            if revision['status'] == 'accepted_complete' or withdrawn or introduced:
                modern.add(event)
                if eligible:
                    selected_modern.add(event)
            if revision['status'] == 'accepted_complete':
                active[event] = set(ids)
                if eligible:
                    selected[event] = set(ids)
            else:
                prior.update(introduced)
                prior.difference_update(withdrawn)
                if eligible:
                    selected.setdefault(event,set()).update(introduced)
                    selected[event].difference_update(withdrawn)
            if eligible:
                evidence.append((event,row['id'],row['completed_at'],revision))
        for key in ids:
            if key in owners and owners[key] != event:
                raise ValueError('assertion is owned by multiple raw events')
            owners[key] = event
    inactive = {key for key,event in owners.items() if key not in selected.get(event,set())}
    return Selection(frozenset(inactive),tuple(evidence))


def exclusion(connection, alias, recorded_as_of=None, run_watermark=_CURRENT):
    """SQL filter placed before ranking/LIMIT; None explicitly means legacy mode."""
    if run_watermark is None:
        return [],[]
    selected = selection(connection,recorded_as_of,None if run_watermark is _CURRENT else run_watermark)
    if not selected.inactive_ids:
        return [],[]
    return [f'{alias}.assertion_id NOT IN (SELECT value FROM json_each(?))'],[json.dumps(sorted(selected.inactive_ids))]


def mark_revision_changed(connection, event_id, completed_at):
    """Publication hook for B: call once inside its deduplicated write transaction.

    Membership/status changes without canonical inserts still invalidate derived
    work. The publisher owns deciding whether a newly inserted run changed state.
    """
    now = _timestamp(completed_at)
    maximum = watermark(connection)
    connection.execute('UPDATE memory_state SET version=version+1 WHERE singleton=1')
    connection.execute("INSERT INTO memory_derived_work(scope,desired_frontier,updated_at,available_at) "
        "SELECT scope,json_object('generation',1,'source_revision_watermark',?),?,? FROM memory_raw_events WHERE id=? "
        "ON CONFLICT(scope) DO UPDATE SET desired_frontier=json_set(desired_frontier,'$.generation',"
        "coalesce(json_extract(desired_frontier,'$.generation'),0)+1,'$.source_revision_watermark',?),"
        "status=CASE WHEN status='running' THEN status ELSE 'pending' END,error=NULL,error_count=0,"
        "updated_at=excluded.updated_at,available_at=excluded.available_at",(maximum,now,now,event_id,maximum))
