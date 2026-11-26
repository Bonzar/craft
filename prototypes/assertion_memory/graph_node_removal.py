"""Owner-confirmed logical deletion of exactly one current canonical thought."""
from contextlib import contextmanager
import hashlib
import json
import re

from .database_clear import generation_scope, revision as database_revision
from .processing_control import Conflict
from .thought_graph import export_graph
from .thought_payload import decode_payload
from .thought_store import ThoughtConflict, ThoughtStore

PATH = '/api/v1/graph-nodes/remove'
PREVIEW_PATH = '/api/v1/graph-nodes/remove-preview'
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}\Z')
MAX_PROOF_HEADS = 5000
REASON = 'owner_removed_single_graph_node'


class NodeConflict(Conflict):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _BoundTransaction:
    def __init__(self, db):
        self.db = db

    @contextmanager
    def transaction(self, *, write=False):
        # The owner dispatcher owns BEGIN IMMEDIATE, rollback, and release leases.
        if not self.db.in_transaction:
            raise ValueError('Graph mutation must use the owner transaction')
        yield self.db


def _read_model(db):
    store = ThoughtStore.__new__(ThoughtStore)
    store.store = _BoundTransaction(db)
    store._source_policy_memo = {}
    return store


def check_request(thought_id, thought_version):
    if (type(thought_id) is not str or not ID.fullmatch(thought_id)
            or type(thought_version) is not int or not 1 <= thought_version <= 2**53-1):
        raise ValueError('Invalid thought identity')


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (name,)).fetchone()


def _verify_no_dependencies(db, thought_id):
    # Any retained external basis claims this exact family as evidence.
    # Never silently invalidate an immutable dependency, including history.
    sql = 'SELECT payload FROM memory_thought_versions WHERE thought_id<>? LIMIT ?'
    for i, row in enumerate(db.execute(sql, (thought_id, MAX_PROOF_HEADS+1))):
        if i >= MAX_PROOF_HEADS:
            raise NodeConflict('dependency_proof_unavailable')
        if any(basis.get('thought_id') == thought_id for basis in decode_payload(row[0])['bases']):
            raise NodeConflict('dependent_thoughts')
    if _table(db, 'memory_thought_work') and db.execute(
        '''SELECT 1 FROM memory_thought_work WHERE thought_id=? AND
           (lease_token IS NOT NULL OR lease_until IS NOT NULL OR
           desired_generation>completed_generation OR status IN ('pending','running'))''',
        (thought_id,)).fetchone():
        raise NodeConflict('thought_work_in_progress')


def _preview(db, *, stage, binding, thought_id, thought_version):
    check_request(thought_id, thought_version)
    if not db.in_transaction:
        raise ValueError('Owner graph snapshot requires a transaction')
    row = db.execute('SELECT v.content_hash FROM memory_thoughts h JOIN memory_thought_versions v '
        'ON v.thought_id=h.id AND v.version=h.current_version WHERE h.id=? AND h.current_version=?',
        (thought_id, thought_version)).fetchone()
    if row is None:
        raise NodeConflict('thought_changed_or_missing')
    thoughts = _read_model(db)
    current = thoughts.get(thought_id)
    if current is None or current.metadata.get('selection_context') is not None:
        raise NodeConflict('thought_not_current_graph_node')
    _verify_no_dependencies(db, thought_id)
    graph_scope = generation_scope(stage, (*binding[0], database_revision(db)))
    graph = export_graph(thoughts)
    if not any(n['thought_ref'] == {'thought_id': thought_id, 'version': thought_version} and n['is_current']
               for n in graph['nodes']):
        raise NodeConflict('thought_not_current_graph_node')
    graph_rev = graph['source_revision']
    token = hashlib.sha256(json.dumps(['graph-node-remove-v1',stage,binding,graph_scope,thought_id,
         thought_version,row[0],graph_rev], separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()[:32]
    return thoughts, dict(stage=stage, thought_id=thought_id, thought_version=thought_version,
        scope=graph_scope, source_revision=graph_rev, revision=token)


def preview(db, *, stage, binding, thought_id, thought_version):
    return _preview(db, stage=stage, binding=binding, thought_id=thought_id,
        thought_version=thought_version)[1]


def remove(db, *, stage, binding, thought_id, thought_version, expected_revision):
    thoughts, state = _preview(db, stage=stage, binding=binding, thought_id=thought_id,
                              thought_version=thought_version)
    if expected_revision != state['revision']:
        raise NodeConflict('node_confirmation_stale')
    try:
        retired = thoughts.retire(thought_id, REASON, {'verification':'accepted',
            'expected_versions':{thought_id:thought_version}, 'stage':stage,
            'scope': state['scope'], 'confirmed_revision': expected_revision})
    except ThoughtConflict as error:
        raise NodeConflict('thought_changed_or_missing') from error
    if retired is None:
        raise NodeConflict('thought_changed_or_missing')
    new = export_graph(_read_model(db))
    if (new['source_revision'] == state['source_revision']
        or any(node['thought_ref']['thought_id']==thought_id for node in new['nodes'])):
        raise NodeConflict('graph_change_unconfirmed')
    return dict(state, removed=True, source_revision=new['source_revision'])
